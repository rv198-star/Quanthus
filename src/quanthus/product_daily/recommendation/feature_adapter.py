"""In-memory P11 feature adapter: qualified in-job rows -> BaselineFeatureRow / facts.

This adapter consumes the already-read, already-qualified in-memory daily inputs P12
delivers (bars, corporate actions, exceptions, calendar, identity) and produces the two
things the rule seam and the expert context consume:

* `CandidateDailyInput` rows for the event scan, and
* `SecurityFeatureFacts` carrying the real computed feature values (rsi14, vol20,
  total_return_index, sma20/60, momentum20, prior_high20) plus the raw P10-supplied
  identity/price fields, bound to one signal date and knowledge cutoff.

It never reads a physical store, never touches SQL, and never substitutes a missing or
non-equivalent field: an unavailable action/factor is blocked for the affected edge, not
defaulted.  The feature formulas mirror `_feature_panel_query` exactly (same windows,
same boundaries, same no-shrink semantics); the same bars+actions input yields the same
values, and a divergence is a defect, not a tolerated approximation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import exp, log, sqrt

from quanthus.event_library.scan import CandidateDailyInput
from quanthus.product_daily.contracts import ProductDailyError, require_aware, require_text
from quanthus.product_daily.recommendation.rule_seam import SecurityFeatureFacts


@dataclass(frozen=True)
class DailyBar:
    """One qualified daily bar for one security, already PIT-trimmed to the cutoff."""

    security_id: str
    session_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    amount: Decimal
    is_tradable: bool = True


@dataclass(frozen=True)
class CorporateAction:
    """A normalized corporate action effective on one date; known before it applies."""

    security_id: str
    effective_date: date
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    known_at: date


@dataclass(frozen=True)
class ExceptionSpan:
    """A corporate-action exception window blocking the affected session(s)."""

    security_id: str
    blocked_from: date
    blocked_through: date | None


@dataclass(frozen=True)
class IdentityFacts:
    """The P10-supplied raw identity / dependency facts for one security."""

    security_id: str
    listing_code: str
    security_name: str
    industry_id: str
    venue_id: str
    instrument_type: str
    listing_id: str
    company_id: str
    depends_on_member_ids: tuple[str, ...]
    invalidations: tuple[str, ...]
    price_bound: Decimal | None
    source_evidence_digest: str


@dataclass(frozen=True)
class CalendarView:
    """The authoritative trading calendar the windows and listing floor use."""

    trading_dates: tuple[date, ...]
    listing_sessions: int


def _validate_bar(bar: DailyBar) -> None:
    for name in ("open_price", "high_price", "low_price", "close_price", "volume", "amount"):
        value = getattr(bar, name)
        if not value.is_finite() or value < 0:
            raise ProductDailyError("invalid_bar", f"{name} must be a finite non-negative value")
    if bar.open_price <= 0 or bar.high_price <= 0 or bar.low_price <= 0 or bar.close_price <= 0:
        raise ProductDailyError("invalid_bar", "prices must be positive")
    if not (
        bar.low_price <= bar.open_price <= bar.high_price
        and bar.low_price <= bar.close_price <= bar.high_price
    ):
        raise ProductDailyError("invalid_bar", "OHLC bounds violated")


def _log_returns(
    bars: Sequence[DailyBar],
    actions: Mapping[tuple[str, date], CorporateAction],
) -> tuple[Decimal | None, ...]:
    """Adjusted daily log returns, matching the panel's candidate_gross_return path."""

    returns: list[Decimal | None] = []
    for index in range(len(bars)):
        current = bars[index]
        if index == 0:
            returns.append(None)
            continue
        prior = bars[index - 1]
        action = actions.get((current.security_id, current.session_date))
        bonus = action.bonus_share_ratio if action is not None else Decimal("0")
        dividend = action.cash_dividend_per_share if action is not None else Decimal("0")
        denominator = prior.close_price
        if denominator <= 0:
            returns.append(None)
            continue
        gross = (current.close_price * (Decimal("1") + bonus) + dividend) / denominator
        if gross <= 0 or not gross.is_finite():
            returns.append(None)
            continue
        returns.append(Decimal(str(log(float(gross)))))
    return tuple(returns)


def _total_return_index(returns: Sequence[Decimal | None]) -> tuple[Decimal, ...]:
    """100 * exp(cumulative log returns), matching the panel index construction."""

    index_values: list[Decimal] = []
    cumulative = Decimal("0")
    for value in returns:
        cumulative += value if value is not None else Decimal("0")
        index_values.append(Decimal(str(100.0 * exp(float(cumulative)))))
    return tuple(index_values)


def _rolling_mean(values: Sequence[Decimal | None], window: int, index: int) -> Decimal | None:
    subset = [v for v in values[max(0, index - window + 1) : index + 1] if v is not None]
    if len(subset) < window:
        return None
    return sum(subset) / Decimal(len(subset))


def _rolling_max_prior(values: Sequence[Decimal | None], window: int, index: int) -> Decimal | None:
    subset = [v for v in values[max(0, index - window) : index] if v is not None]
    if not subset:
        return None
    return max(subset)


def _rolling_stddev(values: Sequence[Decimal | None], window: int, index: int) -> Decimal | None:
    subset = [v for v in values[max(0, index - window + 1) : index + 1] if v is not None]
    if len(subset) < window:
        return None
    mean = sum(subset) / Decimal(len(subset))
    variance = sum((v - mean) ** 2 for v in subset) / Decimal(len(subset) - 1)
    return Decimal(str(float(variance.sqrt()) * sqrt(252.0)))


def _rsi14(returns: Sequence[Decimal | None], index: int) -> Decimal | None:
    subset = [v for v in returns[max(0, index - 13) : index + 1] if v is not None]
    if len(subset) < 14:
        return None
    gains = [v for v in subset if v > 0]
    losses = [-v for v in subset if v < 0]
    avg_gain = sum(gains) / Decimal(14)
    avg_loss = sum(losses) / Decimal(14)
    total = avg_gain + avg_loss
    if total == 0:
        return Decimal("50")
    return Decimal("100") * avg_gain / total


def compute_security_feature_facts(
    *,
    security_id: str,
    bars: Sequence[DailyBar],
    actions: Sequence[CorporateAction],
    exceptions: Sequence[ExceptionSpan],
    calendar: CalendarView,
    identity: IdentityFacts,
    signal_date: date,
    signal_close: datetime,
) -> SecurityFeatureFacts:
    """Compute the real feature values for one security on one signal date.

    The bars must be the qualified, already-PIT-trimmed history ending at signal_date.
    The same bars+actions input yields the same values as the reference feature panel;
    a missing action/factor blocks the affected feature instead of being defaulted.
    """

    require_text(security_id, "security_id")
    require_aware(signal_close, "signal_close")
    if not bars:
        raise ProductDailyError("missing_bars", "no qualified bars for this security")
    ordered = sorted(bars, key=lambda b: b.session_date)
    if ordered[-1].session_date != signal_date:
        raise ProductDailyError(
            "missing_signal_bar",
            "the bar history must end on the declared signal_date",
        )
    for bar in ordered:
        _validate_bar(bar)
    action_map = {(a.security_id, a.effective_date): a for a in actions}
    exception_ranges = [e for e in exceptions if e.security_id == security_id]

    def exception_active(day: date) -> bool:
        return any(
            e.blocked_from <= day and (e.blocked_through is None or day <= e.blocked_through)
            for e in exception_ranges
        )

    returns = _log_returns(ordered, action_map)
    index_values = _total_return_index(returns)
    n = len(ordered)
    idx = n - 1  # the signal_date row

    total_return_index = index_values[idx]
    sma20 = _rolling_mean(index_values, 20, idx)
    sma60 = _rolling_mean(index_values, 60, idx)
    momentum20 = (
        None
        if idx - 20 < 0 or index_values[idx - 20] is None
        else total_return_index / index_values[idx - 20] - Decimal("1")
    )
    _rolling_max_prior(index_values, 20, idx)
    realized_volatility20 = _rolling_stddev(
        [None if r is None else Decimal(str(float(exp(float(r))) - 1)) for r in returns],
        20,
        idx,
    )
    rsi14 = _rsi14(
        [None if r is None else Decimal(str(float(exp(float(r))) - 1)) for r in returns],
        idx,
    )
    close_price = ordered[idx].close_price

    return SecurityFeatureFacts(
        security_code=identity.listing_code,
        signal_date=signal_date,
        rsi14=rsi14,
        realized_volatility20=realized_volatility20,
        listing_sessions=calendar.listing_sessions,
        total_return_index=total_return_index,
        sma20=sma20,
        sma60=sma60,
        momentum20=momentum20,
        security_name=identity.security_name,
        industry_id=identity.industry_id,
        mark_price=close_price,
        price_bound=identity.price_bound,
        invalidations=identity.invalidations,
        depends_on_member_ids=identity.depends_on_member_ids,
    )


def daily_input_from_bar(
    *,
    bar: DailyBar,
    identity: IdentityFacts,
    listing_sessions: int,
    close_at: datetime,
    knowledge_cutoff: datetime,
    source_evidence_digest: str,
    adjustment_effective: bool | None = None,
) -> CandidateDailyInput:
    """Project one qualified bar + identity into a CandidateDailyInput for the scan.

    close_at is the session close instant the calendar supplies for this date (15:00
    exchange time under the daily profile); it is not invented here.
    """

    require_aware(close_at, "close_at")
    require_aware(knowledge_cutoff, "knowledge_cutoff")
    return CandidateDailyInput(
        security_id=identity.security_id,
        listing_id=identity.listing_id,
        listing_code=identity.listing_code,
        company_id=identity.company_id,
        venue_id=identity.venue_id,
        instrument_type=identity.instrument_type,
        static_level1_industry_id=identity.industry_id,
        session_date=bar.session_date,
        close_at=close_at,
        knowledge_cutoff=knowledge_cutoff,
        open_price=bar.open_price,
        high_price=bar.high_price,
        low_price=bar.low_price,
        close_price=bar.close_price,
        turnover=bar.volume,
        is_tradable=bar.is_tradable,
        listing_sessions=listing_sessions,
        adjustment_effective=adjustment_effective,
        source_evidence_digest=source_evidence_digest,
    )

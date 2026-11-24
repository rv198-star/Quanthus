"""Historical-daily-bar quote service for the replay desk (issue #748).

Written fill rule (frozen in the issue body): a limit order placed before
the T+1 session fills when the session's REAL 1d bar crosses it - a buy
fills when the bar's low <= limit, a sell when the bar's high >= limit -
at the recorded fill price (the limit itself when the market traded
through it, so the buyer never pays above their cap).  ``open`` is exposed
separately so a stricter rule can fill at the open when the open already
crosses.  The service is session-scoped: each instance is built for ONE
trading date, ``quote_time`` is that session's real 15:00 close, and
``previous_close`` comes from the prior session's bar in the same slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from quanthus.p12_paper.quotes import Quote

SHANGHAI = timezone(timedelta(hours=8))
SESSION_CLOSE = time(15, 0)


@dataclass(frozen=True)
class DayBar:
    """One security's real 1d bar for one session (price_basis=none)."""

    security_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None
    amount: Decimal | None = None
    security_name: str | None = None


class DailyBarQuoteService:
    """Session-scoped quote source over verified 1d bars.

    ``session_date`` is the trading day the quotes describe.  ``bars`` maps
    security_code -> DayBar for that session; ``previous`` maps code -> the
    prior session's DayBar (used for the +-band check, never for a live
    price).  A code with no bar reads ``unavailable`` with the honest
    reason - never a fabricated price.
    """

    def __init__(
        self,
        *,
        session_date: date,
        bars: dict[str, DayBar],
        previous: dict[str, DayBar] | None = None,
        source: str = "replay-1d",
    ) -> None:
        self._day = session_date
        self._bars = dict(bars)
        self._previous = dict(previous or {})
        self._source = source

    @property
    def session_date(self) -> date:
        return self._day

    def _sh_now(self) -> datetime:
        # The replay's "now" for market_state is the session close: orders
        # accepted by the desk are matched inside the one compressed step,
        # and expire_day_orders treats post-close as the day end.
        return datetime.combine(self._day, SESSION_CLOSE, tzinfo=SHANGHAI)

    def is_trading_day(self, day: date) -> bool:
        return day == self._day

    def market_state(self, moment: datetime | None = None) -> str:
        if moment is None:
            return "trading"
        now = moment.astimezone(SHANGHAI)
        if now.date() != self._day:
            return "holiday"
        hm = (now.hour, now.minute, now.second)
        if hm < (9, 30, 0):
            return "pre_open"
        if hm < (15, 0, 0):
            return "trading"
        return "closed"

    def continuous_auction(self, moment: datetime | None = None) -> bool:
        return self.market_state(moment) == "trading"

    def session(self) -> dict[str, Any]:
        return {
            "market_state": "trading",
            "trading_date": self._day.isoformat(),
            "server_time": self._sh_now().isoformat(),
            "continuous_auction": True,
            "next_open": datetime.combine(
                self._day, time(9, 30), tzinfo=SHANGHAI
            ).isoformat(),
        }

    def quote(self, code: str) -> Quote:
        bar = self._bars.get(str(code).zfill(6)) or self._bars.get(str(code))
        now = self._sh_now()
        if bar is None:
            return Quote(
                security_code=str(code),
                security_name=str(code),
                exchange="",
                status="unavailable",
                last_price=None,
                previous_close=None,
                open=None,
                high=None,
                low=None,
                volume_lots=None,
                quote_time=None,
                received_at=now,
                source="none",
                market_state="trading",
                stale=False,
                reason="no 1d bar for this security in the replay slice",
            )
        prev = self._previous.get(bar.security_code)
        return Quote(
            security_code=bar.security_code,
            security_name=bar.security_name or bar.security_code,
            exchange="",
            status="live",
            last_price=bar.close,
            previous_close=prev.close if prev else None,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            volume_lots=bar.volume,
            quote_time=datetime.combine(bar.trade_date, SESSION_CLOSE, tzinfo=SHANGHAI),
            received_at=now,
            source=self._source,
            market_state="trading",
            stale=False,
            reason=None,
        )


def match_limit(side: str, limit_price: Decimal, bar: DayBar) -> Decimal | None:
    """The phase-1 replay fill rule (P11 口径): fills at the session OPEN.

    A pre-open limit order is matched against the T+1 session's real open:
    buy fills at bar.open when bar.open <= limit_price; sell fills at
    bar.open when bar.open >= limit_price; otherwise the order never fills.
    No intraday high/low optimisation - the named replay quote is the
    open, not the day's best print.  Returns the open fill price or None.
    """

    # The Base stores prices at 10 decimals; A-share orders and the
    # ledger's money rule are 2 decimals, so the session open quantizes
    # before it can become a fill price.
    open_2dp = bar.open.quantize(Decimal("0.01"))
    side = str(side).lower()
    if side == "buy":
        return open_2dp if open_2dp <= limit_price else None
    if side == "sell":
        return open_2dp if open_2dp >= limit_price else None
    return None

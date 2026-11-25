"""Replay a completed session's quotes on one shared clock.

``Replay`` binds a saved intraday tape to a controllable clock so a replay
run serves the suggestion, the quotes and the simulated match from the same
point in time.  The tape is captured once by ``capture`` (real quotes +
minute series for one day) and frozen to JSON; the matcher then only ever
sees quotes already visible at the replay instant, never a live feed.

This is the quote entry point extracted from the R4 private replay driver
(``artifacts/p12/experience-20261120/preview/replay-preview.py``) so the
local server can wire suggestion + quotes + match to one replay clock.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace
from datetime import datetime
from threading import Lock
from typing import Any

from quanthus.p12_paper.quotes import IntradaySeries, QuoteService


class Replay:
    """Advance a frozen tape through a session, one completed minute at a time.

    ``tape`` maps security_code -> {"name", "previous_close", "points"}, the
    exact shape ``capture`` writes.  ``now()`` is the shared replay clock
    (also injected as the kernel ``clock``), ``fetch_raw`` feeds the admitted
    ``QuoteService`` fetcher, and ``fetch`` serves the intraday endpoint, so a
    resting limit order only ever fills on a quote already visible at the
    replay instant.
    """

    def __init__(
        self,
        tape: dict[str, Any],
        day: str,
        source: str,
        start_at: datetime | None = None,
    ) -> None:
        self._tape = tape
        self._day = day
        self._source = source
        self._times = {
            code: [IntradaySeries._minute_end(row["time"]) for row in info["points"]]
            for code, info in tape.items()
        }
        close = datetime.fromisoformat(day + "T15:00:00+08:00")
        self._timeline = sorted({close, *(t for times in self._times.values() for t in times)})
        self._override: datetime | None = None
        self._lock = Lock()
        if start_at is None:
            self._index = 0
        elif start_at > self._timeline[-1]:
            self._index = len(self._timeline) - 1
            self._override = start_at
        else:
            self._index = self._timeline.index(start_at)

    def now(self) -> datetime:
        # An override carries the clock past the tape's last tick (e.g. into
        # the next session for the "nothing today / input not ready" day or a
        # fully-expired plan), while quotes still read only tape-visible data.
        if self._override is not None:
            return self._override
        return self._timeline[self._index]

    def clock(self, advance: bool) -> dict[str, Any]:
        with self._lock:
            if advance and self._index < len(self._timeline) - 1:
                self._index += 1
                self._override = None
            return {
                "time": self.now().isoformat(),
                "finished": self._index == len(self._timeline) - 1,
            }

    def set_now(self, instant: datetime) -> None:
        """Jump the replay clock to a timeline instant (or the latest <= it).

        An instant beyond the tape's close is held as an override: the shared
        clock keeps moving (so a published next-day package or an expired
        window reads correctly) while quotes keep serving the last tape tick.
        """
        with self._lock:
            if instant > self._timeline[-1]:
                self._override = instant
                return
            self._override = None
            idx = bisect_right(self._timeline, instant) - 1
            self._index = max(0, min(idx, len(self._timeline) - 1))

    def fetch_raw(self, code: str):
        now = self.now()
        info = self._tape[code]
        count = bisect_right(self._times[code], now)
        if not count:
            raise LookupError("本分钟前无可用历史行情")
        point = info["points"][count - 1]
        fields = [""] * 40
        fields[1:5] = [info["name"], code, point["price"], info["previous_close"]]
        # Field 5 is the CURRENT session's OPEN print - the bound session's
        # qualified 09:30 opening price (the continuous-auction open), never a
        # later last-price tick, an earlier session's print on a multi-day
        # tape, a PRE-OPEN imprint such as a 09:29 52.00 tick stamped on the
        # same day (Issue #723 E8 r5 fix-2), or a LATER bar merely stamped at
        # or after the 09:30 mark (Issue #723 E8 r5 fix-4).  The open must be
        # the tape's real 09:30 minute-bar - the point whose minute closes at
        # 09:31 - AND it must already be VISIBLE at the replay clock, so a
        # later not-yet-visible 09:32 quote never pre-fills the open before
        # its own timestamp, and a tape that has no true 09:30 bar at all
        # leaves the open empty for the named rejection.
        points = info["points"][:count]
        day_digits = now.strftime("%Y%m%d")
        # The session's qualified open is the minute-bar that ENDS at 09:31 -
        # the 09:30-09:31 continuous-auction opening minute.  Comparing the
        # bar's minute-end keeps it independent of the tape's stored label
        # convention while still matching exactly one real bar; anything
        # earlier (pre-open) or later (a 09:32+ last-price tick) is excluded.
        open_minute_end = datetime(
            now.year, now.month, now.day, 9, 31, tzinfo=now.tzinfo
        )
        open_point = next(
            (
                row
                for row in points
                if str(row.get("time", "")).startswith(day_digits)
                and IntradaySeries._minute_end(str(row.get("time", "")))
                == open_minute_end
            ),
            None,
        )
        fields[5] = open_point["price"] if open_point is not None else ""
        fields[6] = point["volume_lots"]
        fields[30] = self._times[code][count - 1].strftime("%Y%m%d%H%M%S")
        return fields, now

    def fetch(self, code: str) -> dict[str, Any]:
        now = self.now()
        count = bisect_right(self._times.get(code, ()), now)
        points = self._tape[code]["points"][:count] if count else []
        return {
            "security_code": code,
            "trade_date": self._day,
            "source": self._source,
            "as_of": now.isoformat(),
            "status": "live" if points else "unavailable",
            "contract_version": "p12-paper-intraday-v1",
            "points": points,
        }

    def fetch_daily_kline(self, code: str) -> dict[str, Any]:
        """Safe offline fallback in replay mode: never reaches out to live feeds."""
        return {
            "security_code": code,
            "source": self._source,
            "basis": "qfq",
            "price_basis": "tencent_qfq_reference",
            "disclaimer": "历史回放模式未附带看盘参考日 K 线",
            "as_of": self.now().isoformat(),
            "status": "unavailable",
            "reason": "历史回放模式仅提供当日分时回放，未加载外网日 K 线",
            "bars": [],
            "contract_version": "p12-paper-daily-kline-v1",
        }


class ReplayQuotes(QuoteService):
    """A QuoteService over the replay fetcher; quotes carry the replay source."""

    def __init__(self, replay: Replay, source: str) -> None:
        super().__init__(fetcher=replay, now=replay.now)
        self._replay = replay
        self._source = source

    def quote(self, code: str):
        quote = super().quote(code)
        return replace(
            quote,
            source=self._source,
            reason=quote.reason if quote.last_price else "该股票未纳入本次历史回放",
        )

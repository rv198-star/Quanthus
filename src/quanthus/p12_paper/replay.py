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
        self._index = self._timeline.index(start_at) if start_at is not None else 0
        self._lock = Lock()

    def now(self) -> datetime:
        return self._timeline[self._index]

    def clock(self, advance: bool) -> dict[str, Any]:
        with self._lock:
            if advance and self._index < len(self._timeline) - 1:
                self._index += 1
            return {
                "time": self.now().isoformat(),
                "finished": self._index == len(self._timeline) - 1,
            }

    def set_now(self, instant: datetime) -> None:
        """Jump the replay clock to a timeline instant (or the latest <= it)."""
        with self._lock:
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
